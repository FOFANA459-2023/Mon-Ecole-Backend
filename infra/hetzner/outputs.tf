output "ipv4" {
  description = "GitHub variable VPS_HOST."
  value       = hcloud_server.api.ipv4_address
}

output "api_host" {
  description = "GitHub variable API_HOST (until the school has its own domain)."
  value       = "api.${replace(hcloud_server.api.ipv4_address, ".", "-")}.sslip.io"
}

output "ssh" {
  description = "Sign in to the server."
  value       = "ssh -i ~/.ssh/mon-ecole-deploy root@${hcloud_server.api.ipv4_address}"
}
