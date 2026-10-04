terraform {
  required_version = ">= 1.6, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "eu-west-2"
  default_tags {
    tags = { Project = "FraudGuard", ManagedBy = "Terraform" }
  }
}

variable "vpc_id" { type = string }
variable "public_subnet_id" { type = string }
variable "ubuntu_ami_id" {
  type        = string
  description = "Reviewed Ubuntu 24.04 amd64 AMI in eu-west-2; pin a specific image."
}
variable "key_pair_name" {
  type        = string
  description = "Existing EC2 key pair name. Never pass the private key to Terraform."
}
variable "ssh_cidr" {
  type        = string
  description = "Your public IPv4 address followed by /32."
  validation {
    condition     = can(cidrnetmask(var.ssh_cidr)) && endswith(var.ssh_cidr, "/32")
    error_message = "SSH must be limited to one IPv4 address (/32)."
  }
}

resource "aws_security_group" "app" {
  name_prefix = "fraudguard-"
  description = "HTTPS demo and restricted operator SSH; no public scoring port"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.app.id
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.ssh_cidr
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each          = toset(["80", "443"])
  security_group_id = aws_security_group.app.id
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "outbound" {
  security_group_id = aws_security_group.app.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_instance" "app" {
  ami                         = var.ubuntu_ami_id
  instance_type               = "t3.small"
  subnet_id                   = var.public_subnet_id
  key_name                    = var.key_pair_name
  vpc_security_group_ids      = [aws_security_group.app.id]
  associate_public_ip_address = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }
  lifecycle { prevent_destroy = true }
  tags = { Name = "fraudguard" }
}

output "public_ip" { value = aws_instance.app.public_ip }
