# op-injected by pre_deploy. Reconciles Chaptarr's ebook library (/mnt/media/books,
# mounted at /books) onto the tablet's managed "Kodiak Library" folder.
RM_HOST=10.1.30.245
RM_USER=root
RM_PW={{ op://kdk-ops/remarkable-device/password }}
RM_LIB_FOLDER=Kodiak Library
