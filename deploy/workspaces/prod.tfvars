# "prod" here means the demo everyone sees, not a production system. It carries
# a second replica so a rollout does not take the demo down, and a larger memory
# limit because pandapower solves the 118-bus case in memory.
replicas     = 2
node_port    = 30900
cpu_limit    = "1000m"
memory_limit = "1Gi"
