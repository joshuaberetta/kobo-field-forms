#!/usr/bin/env bash
# Run kobo-field-forms on a fresh DigitalOcean droplet, end to end.
#
#   deploy/droplet.sh create     droplet + firewall (inbound SSH from your IP only)  ← billing
#   deploy/droplet.sh push       copy this checkout to the droplet and build the image
#   deploy/droplet.sh test [..]  run `kff e2e` there (args go to kff e2e), fetch the report
#   deploy/droplet.sh web        start the web interface and tunnel it to localhost:8765
#   deploy/droplet.sh vlm        download Gemma 4 E4B (~5 GB) and start the vision model
#   deploy/droplet.sh ssh        a shell on the droplet
#   deploy/droplet.sh destroy    delete the droplet and firewall                    ← stops billing
#   deploy/droplet.sh all [..]   create, push, test, destroy (destroys even if the test fails)
#
# KOBO_TOKEN and KOBO_BASE_URL are read from your environment and piped to the remote
# command on stdin, so they never land in a file, an image or the remote process list.
# Overrides: NAME, SIZE (s-2vcpu-4gb; use s-8vcpu-16gb-amd with the vlm), REGION, and
# SSH_PORT=443 on networks that block outgoing port 22 (sshd then listens on both).
set -euo pipefail
cd "$(dirname "$0")/.."

NAME=${NAME:-kff-e2e}
SIZE=${SIZE:-s-2vcpu-4gb}
REGION=${REGION:-}
REGION_CANDIDATES="ams3 fra1 lon1 nyc3 tor1 sfo3"
IMAGE=ubuntu-24-04-x64
SSH_KEY_FILE=${SSH_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}
SSH_PORT=${SSH_PORT:-22}
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30)
VLM_FILES="ggml-org/gemma-4-E4B-it-GGUF:gemma-4-E4B-it-Q4_0.gguf ggml-org/gemma-4-E4B-it-GGUF:mmproj-gemma-4-E4B-it-Q8_0.gguf"

droplet_id() { doctl compute droplet list --format ID,Name --no-header | awk -v n="$NAME" '$2 == n {print $1}'; }
droplet_ip() {
    local id; id=$(droplet_id)
    [[ -n "$id" ]] || { echo "No droplet named $NAME (deploy/droplet.sh create)" >&2; exit 1; }
    doctl compute droplet get "$id" --format PublicIPv4 --no-header
}
remote() { ssh -p "$SSH_PORT" "${SSH_OPTS[@]}" "root@$(droplet_ip)" "$@"; }

cloud_init() {
    cat <<YAML
#cloud-config
package_update: true
packages: [docker.io, docker-compose-v2, rsync]
write_files:
  - path: /etc/ssh/sshd_config.d/10-kff-port.conf
    content: |
      Port 22
      Port $SSH_PORT
runcmd:
  # Ubuntu 24.04 starts sshd from ssh.socket, which ignores Port lines (kobo-in-a-box).
  - [sh, -c, 'if [ "$SSH_PORT" != 22 ]; then systemctl disable --now ssh.socket; systemctl enable ssh.service; systemctl restart ssh.service; fi']
  - systemctl enable --now docker
  - mkdir -p /opt/kff /opt/models
YAML
}

create() {
    if [[ -n "$(droplet_id)" ]]; then echo "Droplet $NAME already exists." >&2; exit 1; fi
    local fingerprint my_ip regions id=
    fingerprint=$(ssh-keygen -E md5 -lf "$SSH_KEY_FILE" | awk '{print $2}' | sed 's/^MD5://')
    if ! doctl compute ssh-key list --format FingerPrint --no-header | grep -qx "$fingerprint"; then
        doctl compute ssh-key import "$NAME-key" --public-key-file "$SSH_KEY_FILE"
    fi
    my_ip=$(curl -fsS https://api.ipify.org)
    if [[ -n "$REGION" ]]; then regions=("$REGION"); else read -r -a regions <<<"$REGION_CANDIDATES"; fi
    echo "Creating $NAME ($SIZE, $IMAGE); SSH allowed from $my_ip only"
    if [[ "${YES:-}" != 1 ]]; then
        read -r -p "This starts billing. Continue? [y/N] " ok
        [[ "$ok" == [yY] ]] || exit 1
    fi
    cloud_init > /tmp/kff-cloud-init.yml
    for region in "${regions[@]}"; do
        if id=$(doctl compute droplet create "$NAME" --size "$SIZE" --region "$region" \
            --image "$IMAGE" --ssh-keys "$fingerprint" --tag-name "$NAME" \
            --user-data-file /tmp/kff-cloud-init.yml --wait --format ID --no-header 2>/tmp/kff-create.err); then
            echo "Created in $region"; break
        fi
        echo "  $region: $(tail -1 /tmp/kff-create.err)"; id=
    done
    [[ -n "$id" ]] || { echo "No region accepted $SIZE" >&2; exit 1; }
    local fw_id inbound="protocol:tcp,ports:22,address:$my_ip/32"
    [[ "$SSH_PORT" == 22 ]] || inbound+=" protocol:tcp,ports:$SSH_PORT,address:$my_ip/32"
    local outbound="protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"
    fw_id=$(doctl compute firewall list --format ID,Name --no-header | awk -v n="$NAME-fw" '$2 == n {print $1}')
    if [[ -z "$fw_id" ]]; then
        doctl compute firewall create --name "$NAME-fw" --tag-names "$NAME" \
            --inbound-rules "$inbound" --outbound-rules "$outbound" >/dev/null
    else
        doctl compute firewall update "$fw_id" --name "$NAME-fw" --tag-names "$NAME" \
            --inbound-rules "$inbound" --outbound-rules "$outbound" >/dev/null
    fi
    local ip; ip=$(droplet_ip)
    echo "Waiting for SSH and cloud-init on $ip..."
    local tries=0
    until ssh -p "$SSH_PORT" "${SSH_OPTS[@]}" "root@$ip" 'cloud-init status --wait >/dev/null 2>&1; docker info >/dev/null 2>&1'; do
        tries=$((tries + 1))
        if ((tries > 60)); then
            echo "No SSH on $ip port $SSH_PORT after 5 minutes. If your network blocks port 22, destroy and retry with SSH_PORT=443." >&2
            exit 1
        fi
        sleep 5
    done
    echo "Droplet ready: $ip"
}

push() {
    # The committed tree only (no data, venv or secrets).
    git archive --format=tar HEAD | remote 'rm -rf /opt/kff/src && mkdir -p /opt/kff && tar -C /opt/kff -xf -'
    remote 'cd /opt/kff && mkdir -p out && chmod 777 out && docker compose build kff'
}

with_token() {
    # Runs "$@" on the droplet with the token from stdin in its environment.
    : "${KOBO_TOKEN:?set KOBO_TOKEN (and KOBO_BASE_URL) first}"
    printf '%s\n%s\n' "$KOBO_TOKEN" "${KOBO_BASE_URL:-global}" | remote \
        "read -r KOBO_TOKEN; read -r KOBO_BASE_URL; export KOBO_TOKEN KOBO_BASE_URL; cd /opt/kff; $*"
}

test_e2e() {
    local args="$*" vlm_env=""
    if remote 'docker compose -f /opt/kff/compose.yaml --profile vlm ps --status running vlm 2>/dev/null | grep -q vlm'; then
        vlm_env="-e KFF_VLM_URL=http://vlm:8080/v1"
    fi
    local status=0
    with_token "docker compose run --rm $vlm_env -e KOBO_TOKEN -e KOBO_BASE_URL kff kff e2e --report out/e2e-report.json $args" || status=$?
    mkdir -p out
    scp -P "$SSH_PORT" "${SSH_OPTS[@]}" "root@$(droplet_ip):/opt/kff/out/e2e-report.json" "out/droplet-${REPORT_NAME:-e2e}-report.json" || true
    echo "Report: out/droplet-${REPORT_NAME:-e2e}-report.json"
    return $status
}

web() {
    with_token 'KOBO_TOKEN=$KOBO_TOKEN KOBO_BASE_URL=$KOBO_BASE_URL docker compose up -d kff'
    echo "Tunnelling http://127.0.0.1:8765/ (Ctrl-C to stop; the container keeps running)"
    ssh -p "$SSH_PORT" "${SSH_OPTS[@]}" -N -L 8765:127.0.0.1:8765 "root@$(droplet_ip)"
}

vlm() {
    for pair in $VLM_FILES; do
        local repo=${pair%%:*} file=${pair#*:}
        remote "test -s /opt/models/$file || curl -fL --retry 3 -o /opt/models/$file https://huggingface.co/$repo/resolve/main/$file"
    done
    remote 'cd /opt/kff && VLM_THREADS=$(nproc) docker compose --profile vlm up -d vlm'
    echo "Waiting for the vision model to load (up to 15 minutes)..."
    if ! remote 'for i in $(seq 180); do curl -fs http://127.0.0.1:8081/health >/dev/null && exit 0; sleep 5; done; exit 1'; then
        remote 'cd /opt/kff && docker compose --profile vlm logs --tail 30 vlm' || true
        echo "The vision model didn't come up." >&2
        return 1
    fi
    echo "Vision model up; deploy/droplet.sh test will use it."
}

destroy() {
    local id; id=$(droplet_id)
    [[ -n "$id" ]] || { echo "No droplet named $NAME"; return 0; }
    if [[ "${YES:-}" != 1 ]]; then
        read -r -p "Destroy $NAME ($id)? [y/N] " ok
        [[ "$ok" == [yY] ]] || exit 1
    fi
    doctl compute droplet delete "$id" --force
    local fw_id; fw_id=$(doctl compute firewall list --format ID,Name --no-header | awk -v n="$NAME-fw" '$2 == n {print $1}')
    [[ -z "$fw_id" ]] || doctl compute firewall delete "$fw_id" --force
    echo "Destroyed $NAME."
}

cmd=${1:-}; shift || true
case "$cmd" in
    create) create ;;
    push) push ;;
    test) test_e2e "$@" ;;
    web) web ;;
    vlm) vlm ;;
    ssh) remote ;;
    destroy) destroy ;;
    all)
        create
        status=0
        { push && test_e2e "$@"; } || status=$?
        YES=1 destroy
        exit $status
        ;;
    *) sed -n '2,17p' "$0"; exit 1 ;;
esac
