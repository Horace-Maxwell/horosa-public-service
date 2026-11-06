FROM python:3.12.15-bookworm@sha256:a594f7e9df8a4c431265125b5f5d90cd1b81c8a8797fefee69d5b3544be11111 AS build
WORKDIR /build
COPY . /build
RUN python prepare_public_container.py --install-in-container /build

FROM python:3.12.15-slim-bookworm@sha256:9901e0a8d75037d8242ed43155cbcb2d1f61be1356383d8054afb59fd50e39c4
COPY --from=build /opt/horosa /opt/horosa
COPY --from=build /opt/horosa/system-libs/ /usr/local/lib/
RUN ldconfig
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/opt/horosa/no-store
WORKDIR /opt/horosa
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=240s --retries=3 CMD ["python", "/opt/horosa/entrypoint.py", "--healthcheck"]
ENTRYPOINT ["python", "/opt/horosa/entrypoint.py"]
