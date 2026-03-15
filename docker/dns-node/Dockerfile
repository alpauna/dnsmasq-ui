FROM ubuntu:22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && apt-get install -y \
    dnsmasq \
    keepalived \
    openssh-server \
    curl \
    iproute2 \
    net-tools \
    iputils-ping \
    dnsutils \
    sudo \
    wireguard-tools \
    iptables \
    && rm -rf /var/lib/apt/lists/*

# Create SSH directories
RUN mkdir -p /run/sshd /root/.ssh && \
    chmod 700 /root/.ssh

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Expose ports
EXPOSE 22 53/tcp 53/udp

# Set entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
