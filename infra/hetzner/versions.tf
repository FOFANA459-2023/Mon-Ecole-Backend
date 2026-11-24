terraform {
  required_version = ">= 1.10"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.50"
    }
  }

  # State is local (terraform.tfstate next to this file, git-ignored). Keep a copy: losing it means
  # importing the server, key and firewall by hand.
}

# The API token comes from the HCLOUD_TOKEN environment variable (Hetzner console → the project →
# Security → API tokens → Read & Write). Never write it in a file of this repository.
provider "hcloud" {}
