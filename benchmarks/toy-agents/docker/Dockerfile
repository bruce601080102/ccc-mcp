FROM python:3.11-bookworm

# Official Codex 0.163.0 Linux x86_64 package. Build context supplies this
# downloaded archive; no private Fixture or host repository is copied here.
COPY codex-package-x86_64-unknown-linux-musl.tar.gz /tmp/codex.tar.gz
RUN echo "582646c6bf0561eb0de540f7fc85b9121b0091a826bc6e96830b7f1722122182  /tmp/codex.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/codex \
    && tar -xzf /tmp/codex.tar.gz -C /opt/codex \
    && rm /tmp/codex.tar.gz \
    && useradd --uid 10001 --create-home --shell /bin/sh acqe

ENV PATH="/opt/codex/bin:/usr/local/bin:/usr/bin:/bin"
USER 10001:10001
WORKDIR /work
