# op-injected by pre_deploy (see komodo/resources.toml). Tablet root password lives
# in 1Password; the LAN IP may drift (consider a DHCP reservation for the Paper Pro).
RM_HOST=10.1.30.245
RM_USER=root
RM_PW={{ op://kdk-ops/remarkable-device/password }}
OLLAMA_URL=http://ollama.kmkdp.com:11434
OCR_MODEL=qwen2.5vl:7b
RM_FOLDER=Kodiak Notebooks
