# EC2 deployment configuration

This defines a **new single-host** deployment in London with an encrypted 20 GiB root disk, IMDSv2, restricted SSH, an Elastic IP and a bootstrap script that checks out a reviewed full Git SHA, installs Docker, creates local secrets and deploys the application with Caddy. It has **not been applied** to the current demo.

## State and inputs

Use Terraform 1.10+ and an existing private, versioned S3 state bucket. Copy `backend.hcl.example` to a private configuration file and replace the bucket name. The backend enables encryption and S3 state locking. Supply `vpc_id`, `public_subnet_id`, `ubuntu_ami_id`, `key_pair_name`, `ssh_cidr` (your IPv4 `/32`) and `source_commit` (a reviewed full 40-character commit SHA containing this release) in a private `terraform.tfvars`.

```bash
terraform init -backend-config=backend.hcl
terraform fmt -check
terraform validate
terraform plan -out=deployment.tfplan
```

Review the saved plan before applying. Existing EC2 resources must be imported and reconciled first; applying this configuration directly creates another server. Elastic IPs, EC2 and storage can incur costs, even with free-plan credits. The lifecycle guards prevent accidental Terraform replacement/deletion while present in configuration; changing the pinned bootstrap commit requests replacement and is intentionally blocked without review. Use the checked application deployment for ordinary updates.

No keys/passwords belong in tfvars or user data. The bootstrap generates application secrets on the server, restricts `.env`, and creates no default login. After provisioning, wait for the EIP association and `cloud-init status --wait`, inspect cloud-init logs, create an administrator through the private password prompt, verify external TLS without bypassing certificate checks, and run the application deployment checks. Record that real run before claiming an applied deployment. State locking, bootstrap, TLS issuance and cloud recovery still need AWS validation.

## Validation status

Formatting passes locally. Provider installation is blocked by local Windows access controls, so local schema validation/plan and AWS execution are not claimed. CI initializes with `-backend=false` and validates the configuration without credentials or resources. The bootstrap is checked for Bash syntax. The S3 bucket/permissions must exist separately; the configuration deliberately does not create a state bucket inside its own state.

The host and coordinator remain single points of failure. See [operations](../docs/OPERATIONS.md) for accounts, trusted proxy rules, backups and recovery. Do not combine the supplied `.proxy-enabled` deployment with an existing Caddy override without review.
