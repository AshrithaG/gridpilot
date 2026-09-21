#!/usr/bin/env bash
# Proves the drift check works, rather than merely existing.
#
# A scheduled plan that nobody reads is not detection. This applies the config,
# changes the cluster behind Terraform's back the way a person under pressure
# would, and asserts that plan reports drift with exit code 2. If someone breaks
# the drift detection later, this test fails.
set -euo pipefail
cd "$(dirname "$0")/.."

TF=${TF:-terraform}
KUBECONFIG_PATH=${KUBECONFIG_PATH:-$($TF output -raw kubeconfig_path 2>/dev/null || true)}
NS=gridpilot

echo "1. plan should be clean right after apply"
set +e
$TF plan -detailed-exitcode -input=false -var-file=workspaces/dev.tfvars >/dev/null 2>&1
code=$?
set -e
if [ "$code" != "0" ]; then
  echo "FAIL: plan reported changes (exit $code) on a freshly applied config" >&2
  exit 1
fi
echo "   clean"

echo "2. change the cluster out of band"
kubectl --context "kind-$($TF output -raw cluster_name 2>/dev/null || echo gridpilot-dev)" \
  -n "$NS" scale deployment/gridpilot --replicas=3 >/dev/null
echo "   scaled deployment/gridpilot to 3 replicas with kubectl"

echo "3. plan must now report drift"
set +e
$TF plan -detailed-exitcode -input=false -var-file=workspaces/dev.tfvars >/dev/null 2>&1
code=$?
set -e
if [ "$code" != "2" ]; then
  echo "FAIL: expected exit code 2 (drift), got $code. The drift check is not working." >&2
  exit 1
fi
echo "   drift detected, exit code 2"

echo "4. apply puts it back"
$TF apply -auto-approve -input=false -var-file=workspaces/dev.tfvars >/dev/null
actual=$(kubectl --context "kind-$($TF output -raw cluster_name 2>/dev/null || echo gridpilot-dev)" \
  -n "$NS" get deployment gridpilot -o jsonpath='{.spec.replicas}')
if [ "$actual" != "1" ]; then
  echo "FAIL: replicas is $actual after apply, expected 1" >&2
  exit 1
fi
echo "   reconciled back to 1 replica"
echo
echo "drift test passed"
