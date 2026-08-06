# Postgres seeded with AdventureWorks (OLTP), 68 base tables across five schemas.
#
# The sample data is downloaded at image build time rather than vendored, so no
# 40 MB blob lives in this repo. Both sources are pinned: the loader scripts to a
# commit SHA, the data to Microsoft's immutable release asset.
FROM postgres:16-bookworm

# lorint/AdventureWorks-for-Postgres, master as of 2026-06-29. Pinned by SHA so a
# rebuild cannot silently pick up a different schema.
ARG AW_REPO_SHA=b474991f0df1c4bf55ca4735eb0254ca0709eed2
ARG AW_ZIP_URL=https://github.com/Microsoft/sql-server-samples/releases/download/adventureworks/AdventureWorks-oltp-install-script.zip

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip ruby dos2unix \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /seed

# install.sql uses psql \copy with paths relative to the working directory, so the
# CSVs have to sit flat in /seed. The find|mv flattens the archive if Microsoft ever
# nests it, rather than failing obscurely at load time.
RUN set -eux; \
    curl -fsSL -o aw.zip "${AW_ZIP_URL}"; \
    unzip -q -o aw.zip; \
    rm aw.zip; \
    find . -mindepth 2 -name '*.csv' -exec mv -t . {} +; \
    base="https://raw.githubusercontent.com/lorint/AdventureWorks-for-Postgres/${AW_REPO_SHA}"; \
    curl -fsSL -o install.sql "${base}/install.sql"; \
    curl -fsSL -o update_csvs.rb "${base}/update_csvs.rb"; \
    ruby update_csvs.rb; \
    rm update_csvs.rb; \
    test -s install.sql; \
    echo "CSV files staged: $(ls -1 *.csv | wc -l)"

COPY init/ /docker-entrypoint-initdb.d/
RUN dos2unix /docker-entrypoint-initdb.d/* && chmod +x /docker-entrypoint-initdb.d/*.sh
