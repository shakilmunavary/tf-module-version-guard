terraform {
  required_version = ">= 1.5.0"
}

provider "aws" {
  region = "us-east-1"
}

# OK: exact pin on the latest published version
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "4.21.0"

  name = "dev-vpc"
  cidr = "10.0.0.0/16"
}

# OUTDATED: pinned well below the latest -> this is what should block the PR
module "security_group" {
  source  = "terraform-aws-modules/security-group/aws"
  version = "4.17.1"

  name        = "dev-sg"
  description = "Dev security group"
  vpc_id      = module.vpc.vpc_id
}

# Local module reference -> ignored by the guard (not a registry source)
module "naming" {
  source = "../modules/naming"
  prefix = "dev"
}
