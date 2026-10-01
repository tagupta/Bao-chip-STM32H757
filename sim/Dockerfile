# ArduPilot build environment pinned to the toolchain ArduPilot CI uses.
# (Host GCC 15 miscompiles the bootloader's strlen into a self-branch.)
FROM ubuntu:22.04
ARG GCC=gcc-arm-none-eabi-10-2020-q4-major
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git python3 python3-pip python3-dev build-essential ca-certificates curl bzip2 \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://firmware.ardupilot.org/Tools/STM32-tools/${GCC}-$(uname -m)-linux.tar.bz2 \
        | tar -xj -C /opt
ENV PATH=/opt/${GCC}/bin:$PATH
RUN pip3 install --no-cache-dir empy==3.3.4 pexpect future intelhex pyserial pymonocypher==3.1.3.2
RUN git config --global --add safe.directory '*'
WORKDIR /work/ardupilot
