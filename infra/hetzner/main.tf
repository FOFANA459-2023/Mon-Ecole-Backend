# Production server on Hetzner Cloud: one VPS running the API with Docker Compose (deploy/vps).
# The database and the files stay on Supabase. See deploy/vps/README.md.

resource "hcloud_ssh_key" "deploy" {
  name       = "mon-ecole-deploy"
  public_key = file(pathexpand(var.ssh_public_key_file))
}

resource "hcloud_firewall" "web" {
  name = "${var.name}-web"

  dynamic "rule" {
    for_each = {
      ssh   = { protocol = "tcp", port = "22" }
      http  = { protocol = "tcp", port = "80" }
      https = { protocol = "tcp", port = "443" }
      http3 = { protocol = "udp", port = "443" }
    }
    content {
      description = rule.key
      direction   = "in"
      protocol    = rule.value.protocol
      port        = rule.value.port
      source_ips  = ["0.0.0.0/0", "::/0"]
    }
  }

  rule {
    description = "ping"
    direction   = "in"
    protocol    = "icmp"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }
}

resource "hcloud_server" "api" {
  name         = var.name
  server_type  = var.server_type
  location     = var.location
  image        = "ubuntu-24.04"
  ssh_keys     = [hcloud_ssh_key.deploy.id]
  firewall_ids = [hcloud_firewall.web.id]
  # First boot: Docker, firewall, automatic security updates, swap (the same script as by hand).
  user_data = file("${path.module}/../../deploy/vps/setup.sh")

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  labels = {
    project     = "mon-ecole"
    environment = "production"
  }

  lifecycle {
    # Changing the first-boot script must not rebuild a running server.
    ignore_changes = [user_data, image]
  }
}
