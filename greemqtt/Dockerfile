FROM monteship/greemqtt:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
      jq bash \
    && rm -rf /var/lib/apt/lists/*

# Copy our patched __main__.py file
COPY __main__.py /app/GreeMQTT/__main__.py

# Copy the debug run.sh script
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD [ "/run.sh" ]