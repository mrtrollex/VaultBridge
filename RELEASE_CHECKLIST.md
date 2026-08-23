# VaultBridge Release Checklist

## Code

- [ ] all tests pass
- [ ] Python compile check passes
- [ ] Docker image builds
- [ ] no debug code or private local paths
- [ ] dependency changes reviewed

## Security

- [ ] no `.env` or secrets in Git history
- [ ] no real vault content in fixtures/docs
- [ ] authentication tests pass
- [ ] traversal/symlink security tests pass
- [ ] public deployment examples require HTTPS

## API

- [ ] OpenAPI/action schema matches actual routes
- [ ] breaking changes documented
- [ ] version bump is correct
- [ ] index rebuild/migration impact documented

## Documentation

- [ ] README quick start verified from a clean checkout
- [ ] generic Docker guide verified
- [ ] TrueNAS guide verified where applicable
- [ ] environment variables documented
- [ ] screenshots contain no secrets/private information

## Distribution

- [ ] Git tag created
- [ ] GitHub release notes written
- [ ] GHCR image published
- [ ] image version tag and `latest` policy verified
- [ ] rollback instructions available
