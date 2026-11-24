variable "name" {
  description = "Server name."
  type        = string
  default     = "mon-ecole-production"
}

variable "location" {
  description = "Data centre: nbg1 (Nuremberg), fsn1 (Falkenstein), hel1 (Helsinki)."
  type        = string
  default     = "nbg1"
}

variable "server_type" {
  description = "cax11 = Arm, 2 vCPU, 4 GB (cheapest that fits). If it is unavailable: cpx22 (x86, 2 vCPU, 4 GB)."
  type        = string
  default     = "cax11"
}

variable "ssh_public_key_file" {
  description = "Public key allowed to sign in as root (its private half goes in the GitHub secret VPS_SSH_KEY)."
  type        = string
  default     = "~/.ssh/mon-ecole-deploy.pub"
}
