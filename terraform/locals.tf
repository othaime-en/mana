locals {
  common_labels = {
    "managed-by"  = "terraform"
    "project"     = "mana"
    "environment" = var.environment
  }

  image_registry = var.image_registry
}
