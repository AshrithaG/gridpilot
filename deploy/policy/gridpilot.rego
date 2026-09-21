# Policy checks that run against the plan, not against the cluster: a violation
# should stop the change before it is made.
#
#   terraform show -json plan.out | conftest test --policy policy -
package main

deny contains msg if {
	r := input.resource_changes[_]
	r.type == "kubernetes_deployment"
	c := r.change.after.spec[_].template[_].spec[_].container[_]
	endswith(c.image, ":latest")
	msg := sprintf("container %q uses the :latest tag, which makes a rollback meaningless", [c.name])
}

deny contains msg if {
	r := input.resource_changes[_]
	r.type == "kubernetes_deployment"
	c := r.change.after.spec[_].template[_].spec[_].container[_]
	not c.resources[0].limits.memory
	msg := sprintf("container %q has no memory limit, so one pod can take the node down", [c.name])
}

deny contains msg if {
	r := input.resource_changes[_]
	r.type == "kubernetes_deployment"
	c := r.change.after.spec[_].template[_].spec[_].container[_]
	not c.readiness_probe
	msg := sprintf("container %q has no readiness probe, so a rollout sends traffic to a pod that is not ready", [c.name])
}

deny contains msg if {
	r := input.resource_changes[_]
	r.type == "kubernetes_deployment"
	sc := r.change.after.spec[_].template[_].spec[_].container[_].security_context[_]
	sc.run_as_non_root == false
	msg := "a container is allowed to run as root"
}
