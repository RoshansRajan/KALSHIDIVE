# Deploying KalshiDive on AWS

Target topology for the containers `docker-compose.yml` already builds. No
infrastructure code is committed yet — this documents the intended shape so
the Terraform, when written, has something to implement.

## Topology

```
        ┌─────────────────────── VPC ────────────────────────┐
        │                                                     │
        │  ┌── public subnets ──┐   ┌── private subnets ──┐  │
   ─────┼─▶│  ALB :443          │──▶│  ECS: web           │  │
Internet│  │  NAT Gateway       │   │  ECS: ingest        │  │
        │  └────────────────────┘   │  ECS: analysis      │  │
        │                            │        │            │  │
        │                            │        ▼            │  │
        │                            │  RDS Postgres 16    │  │
        │                            │  (Multi-AZ)         │  │
        │                            └─────────────────────┘  │
        └─────────────────────────────────────────────────────┘
```

Only the ALB is public. `ingest` and `analysis` sit in private subnets and
reach Kalshi and the Anthropic API outbound through the NAT gateway.

## Services

| Service | Task size | Count | Notes |
|---|---|---|---|
| `ingest` | 0.5 vCPU / 1 GB | **1** | See the singleton constraint below. |
| `analysis` | 0.25 vCPU / 512 MB | 1–N | Stateless; scale on window backlog. |
| `web` | 0.25 vCPU / 512 MB | 2 | Behind the ALB. |

### `ingest` must run as a singleton

Two ingest tasks subscribed to the same markets would each write every tick,
duplicating history and racing on `feed_cursor`. Run it with
`desiredCount: 1` and **`maximumPercent: 100` / `minimumHealthyPercent: 0`**
so deploys stop the old task before starting the new one.

That deliberately accepts a brief gap in coverage during deploys. The
alternative — overlapping tasks — corrupts the recorded history, which is
worse and not repairable after the fact. If continuous coverage becomes a
requirement, the fix is leader election (a lock row in Postgres, or DynamoDB),
not a second task.

## Data

**RDS Postgres 16**, Multi-AZ, `gp3`. Apply `db/migrations/001_init.sql` once
at provisioning; compose applies it automatically via
`docker-entrypoint-initdb.d`, but RDS has no equivalent hook.

Tick tables grow quickly — a single active market can produce millions of rows
per day. Before this runs long, partition `market_ticks` by month
(`PARTITION BY RANGE (received_at)`) so old data can be dropped by detaching a
partition instead of a `DELETE` that has to rewrite the table.

## Secrets

Store in **AWS Secrets Manager** and inject via the task definition's `secrets`
block, which surfaces them as environment variables at start:

| Secret | Used by |
|---|---|
| `kalshidive/anthropic-api-key` | `analysis` |
| `kalshidive/kalshi-api-key-id` | `ingest` |
| `kalshidive/kalshi-private-key` | `ingest` |
| `kalshidive/database-url` | both |

The Kalshi private key is the awkward one: the code reads it from a *path*
(`KALSHI_PRIVATE_KEY_PATH`), while Secrets Manager injects *values*. Either
write it to a file in an entrypoint script, or add a small branch that accepts
`KALSHI_PRIVATE_KEY_PEM` directly. Passing a private key as an environment
variable does expose it to `docker inspect` and process listings, which is why
the local path-based approach is the default.

## The internal fanout does not survive this topology as-is

Locally, `analysis` reaches `ingest` at `ws://ingest:8765`. On ECS that name
doesn't resolve, and the fanout is in-memory — a single subscriber set inside
one process, invisible to any other task.

Two options, in order of preference:

1. **ECS Service Connect** (or Cloud Map) gives `ingest` a stable DNS name and
   the current code works unchanged. Right answer while `ingest` is a
   singleton.
2. **Replace the fanout with a real broker** — SNS→SQS, MSK, or Redis pub/sub —
   once more than one producer or consumer needs to participate. The
   `packages/bus` interface is deliberately small so this swap touches one
   package rather than every service.

Do not skip this step and assume it works. It won't.

## Observability

Ship container logs to CloudWatch (`awslogs` driver). The signals worth
alarming on:

| Signal | Why it matters |
|---|---|
| `sequence gap` warnings | Occasional is normal; sustained means the book is chronically wrong. |
| Time since last tick | The failure that looks most like a quiet market. Alarm at ~2 min. |
| `flush failed` | Ticks are being dropped — history is developing holes. |
| Analysis validation failures | Model or prompt drift. |
| RDS free storage | Tick tables grow fast; this is the one that pages you at 3am. |

"Time since last tick" is the most valuable of these: every other failure mode
here is loud, but a feed that silently stops looks exactly like a market where
nothing is happening.

## Rough cost

Around **$120–170/month** at idle in `us-east-1`: RDS `db.t4g.small` Multi-AZ
(~$60), three small Fargate tasks (~$35), ALB (~$20), NAT Gateway (~$35).

The NAT gateway is a third of that and exists solely for outbound calls to
Kalshi and Anthropic. Running the tasks in public subnets with security groups
locked to egress-only removes it, at the cost of a weaker network boundary —
a reasonable trade for a personal deployment, not for anything handling real
positions.
