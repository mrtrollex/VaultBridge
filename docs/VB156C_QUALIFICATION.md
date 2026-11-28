# VB-156C qualification addendum

The owner accepted this narrow **VB-156C availability exception** on 2026-11-27. It applies to the
competing deliberate unlock at the tested HTTPS `/proxy` prefix on Windows 11 build 26300 with
Playwright 1.63.0 and Firefox 155.0 revision 1543.

The first tab settled its POST, wrote `restorable`, and released the exclusive Web Lock. The second
tab acquired the lock while its local view still showed `posting`. It entered uncertainty quarantine,
kept protected state blank, and issued no second POST, retry, or credential replay. The `restorable`
storage event arrived afterward. The refused second unlock is the accepted availability limitation.

Keep the required [ADR 0011 session and security guarantees](adr/0011-named-knowledge-spaces-dashboard.md#cross-tab-session-lifecycle-and-cookie-mutation-inventory)
and the existing quarantine and operator recovery behavior. The observed stale-dirty read leaves
the stale-clean case open. Any evidence that stale clean journal state or stale logout intent permits
protected access, rendering, or cookie mutation remains a security blocker. All other qualification
gates apply. [ADR 0012's VB-156B qualification boundaries](adr/0012-vb-156b-qualification-amendment.md)
continue to apply within their accepted scope. VB-156E must reassess this exception before release
qualification.

## Evidence and status

All 72 VB-156C browser cases passed on each of the five eligible profiles. The full Windows Firefox
run retains **324 passes and one failure**, the
`test_two_deliberate_logins_serialize_without_automatic_replay[prefix-https-firefox]` case. A repeat
run reproduced the stale-dirty sequence in one of 24 trials, with instrumentation. All 24 clean-baseline
probes passed. Whether VB-156C affects the frequency remains open. The failure stands as recorded.

The browser evidence identifies the tested input bytes by native source archive SHA-256
`e076c3f8c4958bbd71bd4f2435119904b72f405b89768e729d34b8ac3667e14f`. The subsequent
`tests/test_ui.py` asset inventory update was outside the browser suite. The owner-acceptance
follow-up changes only documentation and evidence metadata.

The original failure JUnit, native traces and reproduction remain in the local `.agent/` evidence.
The trace digests identify the raw artifacts, which are kept local.

| Local artifact | SHA-256 |
| --- | --- |
| `vb156c-windows-firefox-final.xml` | `f2d5330301634060a37ef05c4333934b006857de0b9f2c0c8a8b3f3137e41398` |
| Failed native `trace.zip` under `vb156c-windows-firefox-final-traces/` | `06275dee5a6a3752587abed15182f4467d7787cbb68c344efb726c43223c3117` |
| `vb156c-current-diagnostic-probe.log` | `44c307a16358d343773c8204bad6c2a2c66d9408e9b87d9e351425eed17bcf14` |
| Reproduction `trace.zip` under `vb156c-current-diagnostic-probe-traces/` | `717770862a9125c444083cbba4087df398846f9c745f8e805a76606380a51e30` |
| `vb156c-browser-evidence.json` | `82c214b5b3dfbd40df1ea8cd79d9942abf375a366a9d49a81f9b68c9f8922bcd` |

The scoped Windows Firefox availability exception is owner-accepted. Independent VB-156C review
and release qualification remain separate gates.
