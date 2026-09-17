# op-injected by pre_deploy. Reconciles the vault's 30-Books/ToTablet/ onto the
# tablet's managed "Kodiak Library" folder.
RM_HOST=10.1.30.245
RM_USER=root
RM_PW={{ op://kdk-ops/remarkable-device/password }}
BOOKS_SUBDIR=30-Books/ToTablet
RM_LIB_FOLDER=Kodiak Library
