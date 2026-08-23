# PostgreSQL Operations

## Backups and Restore

Create logical backups with pg_dump, archive WAL files, and apply a retention policy. Restore the PostgreSQL database into a clean instance and verify every recovered table. Záloha databázy a obnova dát používajú rovnaký kontrolný postup.

## Streaming Replication

Streaming replication sends WAL records from the primary PostgreSQL server to a standby replica. Monitor replication lag, slots, failover readiness, and the replay position before promoting the standby.
