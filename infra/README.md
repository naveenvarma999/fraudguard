# EC2 infrastructure reference

This defines a **new**, single-host EC2 deployment in London. It has not been applied to the existing demo. It is not multi-host availability or autoscaling. Resources and public IPv4 addresses can incur charges; free-plan credits are not a cost guarantee.

Supply an existing VPC and public subnet (with internet-gateway routing), a reviewed Ubuntu 24.04 amd64 AMI in eu-west-2, an existing key pair name, and your public IPv4 `/32` in a private `terraform.tfvars`. No credentials, PEM files, application secrets or Terraform state belong in Git.

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan -out=deployment.tfplan
```

Review the plan before applying it. **Do not apply this against your existing EC2 server without importing its resources and reviewing the resulting plan**; otherwise it creates another server. This file does not install or deploy the application. After provisioning, follow [operations](../docs/OPERATIONS.md) for Docker, security configuration, Caddy and deployment checks. Port 8000 remains private, SSH allows a single address, the root disk is encrypted and IMDSv2 is required.

`prevent_destroy` guards accidental Terraform deletion while the resource remains in configuration. A production team should configure access-controlled remote state with locking and separate environments before collaborative use. No backend or paid resources are created automatically by `init` or `validate`.
