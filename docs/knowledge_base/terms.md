# AbyFlow Workspace and Multi-Tenancy Terms

## Personal Workspace
Every registered user automatically receives a personal workspace upon registration.

## Workspace Roles and Permissions
- **Owner**: Full administrative control over workspace settings, customers, invoices, members, and billing.
- **Admin**: Can manage customers, create and issue invoices, and invite team members.
- **Member**: Read and draft access within the workspace.

## Tenant Isolation Guarantee
Customer and invoice records are strictly partitioned by `tenant_id` at the database query and repository layer. Cross-tenant access is strictly denied and audited.

## Platform Roles
Platform roles (`user`, `operator`, `admin`) are strictly decoupled from workspace roles (`owner`, `admin`, `member`). Workspace owners have no operator privileges on the platform.

