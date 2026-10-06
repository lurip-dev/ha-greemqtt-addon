ARG BUILD_FROM
FROM ${BUILD_FROM}

# py3-cryptography comes prebuilt from Alpine for every architecture (no Rust toolchain needed).
RUN apk add --no-cache python3 py3-pip py3-cryptography

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY gree_mqtt /app/gree_mqtt
COPY run.sh /run.sh
RUN chmod a+x /run.sh

WORKDIR /app
CMD [ "/run.sh" ]
