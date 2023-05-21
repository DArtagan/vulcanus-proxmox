terraform {
  required_version = ">= 0.13"

  required_providers {
    github = {
      source = "integrations/github"
      version = ">= 5.9.1"
    }
    flux = {
      source = "fluxcd/flux"
      version = "1.0.0-rc.3"
    }
    tls = {
      source = "hashicorp/tls"
      version = ">= 4.0.4"
    }
  }
}


provider "github" {
  owner = var.github_owner
  token = var.github_token
}


data "github_repository" "main" {
  full_name = join("/", [var.github_owner, var.repository_name])
}

resource "github_branch_default" "main" {
  repository = data.github_repository.main.name
  branch = var.branch
}

resource "tls_private_key" "main" {
  algorithm = "ECDSA"
  ecdsa_curve = "P256"
}

resource "github_repository_deploy_key" "main" {
  title = "piraeus-fluxcd"
  repository = data.github_repository.main.name
  key = tls_private_key.main.public_key_openssh
  read_only = false
}

provider "flux" {
  kubernetes = {
    config_path = var.kubeconfig_path
  }
  git = {
    url = "ssh://git@github.com/${data.github_repository.main.full_name}.git"
    ssh = {
      username = "git"
      private_key = tls_private_key.main.private_key_pem
    }
  }
}

resource "flux_bootstrap_git" "main" {
  depends_on = [github_repository_deploy_key.main]
  path = var.target_path
  components_extra = [
    "image-automation-controller",
    "image-reflector-controller"
  ]
}
