# Local backend for development
# To switch to S3 for production, replace with:
#
# terraform {
#   backend "s3" {
#     bucket         = "mana-terraform-state"
#     key            = "production/terraform.tfstate"
#     region         = "us-west-2"
#     dynamodb_table = "mana-terraform-locks"
#     encrypt        = true
#   }
# }

terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}