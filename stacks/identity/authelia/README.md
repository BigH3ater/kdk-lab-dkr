configuration.yml lands here during identity cutover: the live k3s configmap
(dmz/authelia) transformed for compose -- ldap://lldap:3890, storage host
authelia-postgres, redis host redis, same OIDC clients and access rules.
It contains no secret values (env + /secrets file refs only).
