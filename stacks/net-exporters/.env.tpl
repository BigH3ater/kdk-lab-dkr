# Rendered by Komodo pre_deploy: op inject -f -i .env.tpl -o .env
UP_UNIFI_CONTROLLER_0_API_KEY={{ op://kdk-cluster/unifi-api-token-ro/credential }}
ADGUARD_USERNAMES={{ op://kdk-cluster/kdk-dns-adguard-home-admin/username }},{{ op://kdk-cluster/kdk-dns-adguard-home-admin/username }},{{ op://kdk-cluster/kdk-dns-adguard-home-admin/username }}
ADGUARD_PASSWORDS={{ op://kdk-cluster/kdk-dns-adguard-home-admin/password }},{{ op://kdk-cluster/kdk-dns-adguard-home-admin/password }},{{ op://kdk-cluster/kdk-dns-adguard-home-admin/password }}
SNMP_V3_PASSWORD={{ op://kdk-cluster/unifi-snmp-ro-v3-user/password }}
