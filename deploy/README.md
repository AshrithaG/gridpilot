# deploy

GridPilot's infrastructure as code: a Kubernetes cluster, the app on it, remote
state with locking, two workspaces, a policy gate on every plan, and a drift
check that is tested rather than assumed.

**What this is and is not.** There is no cloud budget behind this project, so the
cluster is [kind](https://kind.sigs.k8s.io/), a real Kubernetes API served from
Docker containers, and the state bucket is MinIO speaking the S3 API. Everything
Terraform-shaped here is the real thing: modules, the `s3` backend with native
locking, workspaces, provider-managed Kubernetes objects, policy on the plan
JSON. What it does **not** demonstrate is the cloud around a cluster: no VPC, no
IAM, no managed load balancer, no node pools, no cost. The public demo still runs
on Render's free tier, which Terraform cannot manage because Render's official
provider only accepts paid plans.

## What was measured

All on one laptop, Terraform 1.16, dev workspace.

| check | result |
|---|---|
| apply from empty state to the demo answering `/api/state` | 46s, cluster creation included |
| destroy leaves nothing behind | no `gridpilot-*` kind clusters remain |
| plan immediately after a rebuild | clean, exit 0 |
| second run while an apply holds the lock | refused: `Error acquiring the state lock`, lock object `terraform.tfstate.tflock` present in the bucket |
| `kubectl scale` to 3 replicas behind Terraform's back | `plan -detailed-exitcode` returns 2; apply restores 1 |
| plan with an image tagged `:latest` | conftest fails it; the compliant plan passes 4 of 4 rules |

## Two things that broke on the way

**The hardening broke the app.** The container runs as a non-root user with a
read-only root filesystem. GridPilot imports pandapower, pandapower imports
matplotlib, and matplotlib refuses to import without a writable config
directory, so the pod crash-looped before serving anything. The readiness probe
kept it out of rotation, which is what it is for. The fix points `HOME`,
`MPLCONFIGDIR` and the XDG directories at the mounted scratch volume rather than
relaxing the filesystem.

**An interrupted apply left the provider confused.** Killing an apply
mid-rollout left the deployment in state with a null resource identity, and the
next apply failed with `Provider produced inconsistent result`. Removing that one
resource from state and letting Terraform recreate it fixed it; the lesson is to
let a stuck rollout time out rather than killing the process.

## Layout

| path | what it does |
|---|---|
| `modules/cluster` | the kind cluster, with the NodePort mapped out to the host, and the local image side-loaded |
| `modules/app` | namespace, config, the optional API key as a secret, the deployment with probes and limits, the service |
| `modules/repo` | branch protection on the GitHub repository as code; **off by default**, because it needs an admin token on a live repository |
| `workspaces/*.tfvars` | dev and prod differ only in replicas, port and limits, not in code |
| `policy/` | conftest rules run on the plan: no `:latest`, memory limits set, readiness probe present, no root |
| `scripts/state_backend.sh` | MinIO as the S3 state bucket |
| `scripts/drift_test.sh` | apply, drift the cluster out of band, require plan to notice, reconcile |
| `scripts/zero_to_serving.sh` | destroy, confirm nothing survives, rebuild, time it |

## Running it

Requires Docker, kind, kubectl, Terraform 1.10 or later, and conftest.

```bash
docker build -t gridpilot:local ..
./scripts/state_backend.sh up
terraform init -backend-config=backend.hcl
terraform workspace select -or-create dev
terraform apply -var-file=workspaces/dev.tfvars
curl -fsS http://127.0.0.1:30800/api/state > /dev/null && echo serving
./scripts/drift_test.sh
```

Moving to real S3 means deleting the endpoint overrides in `backend.hcl`.
Moving to a managed cluster means replacing `modules/cluster`; nothing above it
changes.
