# op-injected by pre_deploy. Reconciles Chaptarr's ebook library (/mnt/media/books,
# mounted at /books) into the tablet's KOReader library dir (/home/root/books/Library)
# as plain files (add + delete). Delivery target is set by RM_BOOKS_DIR in compose.
RM_HOST=10.1.30.245
RM_USER=root
RM_PW={{ op://kdk-ops/remarkable-device/password }}
