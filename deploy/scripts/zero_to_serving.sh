#!/usr/bin/env bash
# Destroy everything, confirm nothing is left behind, rebuild from an empty
# state, and time how long it takes until the demo answers. The number this
# prints is the one the README quotes; it is measured, not estimated.
set -euo pipefail
cd "$(dirname "$0")/.."
TF=${TF:-terraform}
VARS=${VARS:-workspaces/dev.tfvars}
PORT=$($TF output -raw url 2>/dev/null | sed 's/.*://' || echo 30800)

t0=$(date +%s)
$TF destroy -auto-approve -input=false -var-file="$VARS" >/dev/null
t1=$(date +%s)
echo "destroy: $((t1 - t0))s"

left=$(kind get clusters 2>/dev/null | grep -c '^gridpilot-' || true)
if [ "$left" != "0" ]; then
  echo "FAIL: $left gridpilot cluster(s) survived destroy" >&2
  exit 1
fi
echo "nothing left behind: no gridpilot clusters"

t2=$(date +%s)
$TF apply -auto-approve -input=false -var-file="$VARS" >/dev/null
for _ in $(seq 1 120); do
  curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/api/state" && break
  sleep 1
done
t3=$(date +%s)
curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/api/state"
echo "zero to serving: $((t3 - t2))s"

set +e
$TF plan -detailed-exitcode -input=false -var-file="$VARS" >/dev/null 2>&1
code=$?
set -e
[ "$code" = "0" ] || { echo "FAIL: plan not clean after rebuild (exit $code)" >&2; exit 1; }
echo "plan clean after rebuild"
