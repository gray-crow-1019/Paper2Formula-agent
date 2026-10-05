FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer python3 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home converter
COPY converter/convert.py /opt/convert.py
ENV HOME=/tmp
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["python3", "/opt/convert.py"]
