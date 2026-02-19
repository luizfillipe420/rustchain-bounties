# Security Policy

Last updated: 2026-02-19

RustChain and BoTTube welcome good-faith security research.

## Safe Harbor

If you act in good faith and follow this policy, Elyan Labs will not pursue legal action for your research.

Good-faith means:

- avoid privacy violations and service disruption
- do not exfiltrate data or steal funds
- report vulnerabilities responsibly
- give maintainers reasonable time to fix issues

## How to Report

Preferred:

- GitHub Private Vulnerability Reporting (Security Advisories)

Alternative:

- Open a private disclosure request via maintainer contact in repository profile

Include:

- affected repo and component
- reproduction steps
- impact and suggested mitigation

## Scope

In scope:

- RustChain core consensus and attestation logic
- wallet transfer and pending confirm flows
- BoTTube API/auth/session/rate-limit paths
- bridge and payout automation code
- Beacon integration and signing paths

Out of scope:

- social engineering
- physical attacks
- denial-of-service against production infrastructure

## Response Targets

- acknowledgment: within 48 hours
- initial triage: within 5 business days
- fix/mitigation plan: within 30-45 days

## Bounty Guidance (RTC)

Rewards are based on severity and exploitability.

- Critical: 2000+ RTC
- High: 800-2000 RTC
- Medium: 300-800 RTC
- Low: 50-300 RTC

Maintainers may add bonuses for clear proofs, reproducible test cases, and patch suggestions.

## Disclosure

Do not publish exploit details before fix coordination or before 90 days, whichever comes first.

## Recognition

Valid reports may receive:

- RTC bounty payout
- Hall of Hunters recognition (optional)
- follow-on hardening bounties
