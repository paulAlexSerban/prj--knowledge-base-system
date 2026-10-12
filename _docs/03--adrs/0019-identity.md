# ADR 0019: A mock IdP, HS256, with the claims the policy reads

Status: accepted for the POC. OIDC is not built.

## Context

The service has to know the caller's groups, clearance, and department. The assignment does not include a real directory. The claim contract is what authorization enforces, not the brand of the signer.

## Decision

`gateway_core.auth` signs and checks HS256 JWTs with `GATEWAY_JWT_SECRET`. Claims used for authorization are subject, groups, clearance, and department. Personas live in `infrastructure/config/principals.yaml`: `employee_eng`, `hr_admin`, `outsider`, and `product_lead`. `POST /dev/token` on the API issues a token when `DEV_TOKEN_ENDPOINT=1`. Compose leaves that endpoint off unless the variable is set. An expired token, a bad signature, a missing claim, or an unknown clearance is rejected. The HTTP body is `unauthorized`. The MCP server checks the same bearer token and does not mint tokens. Agents do not receive a store credential.

## Alternatives rejected

- MCP as the identity boundary. Rejected in [ADR 0018](0018-authorization.md).
- A baked-in user per demo script, with no token. The two adapters would not share a principal, and a notebook could not call as someone else.
- A local Keycloak or Cognito emulator. More machinery than four personas. Not evaluated. The signer is one module so the replacement is one file.

## Consequences

- Anyone who can read `GATEWAY_JWT_SECRET` can mint a principal. That secret is a dev secret. It is not an identity provider.
- `outsider` has no groups, so retrieval returns nothing. That is a fixture, not a directory.

## Production path

Cognito or the company OIDC provider. API Gateway or the load balancer can validate the token. The gateway still builds the principal from the same claims. `gateway_core.auth` is the only module that knows the signer.
