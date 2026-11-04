FROM python:3.13-slim AS runtime
WORKDIR /opt/diamond
COPY pyproject.toml ./
ARG DIAMOND_EXTRAS=postgres
RUN python -c "import os,sys,tomllib,subprocess; p=tomllib.load(open('pyproject.toml','rb')); d=p['project']['dependencies']+['setuptools>=75','wheel']; [d.extend(p['project']['optional-dependencies'][x]) for x in os.environ.get('DIAMOND_EXTRAS','postgres').split(',') if x]; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*d])"
COPY src ./src
RUN pip install --no-deps --no-build-isolation . && useradd --create-home --uid 10001 diamond && mkdir /state && chown diamond /state
COPY LICENSE THIRD_PARTY_NOTICES.md ./
USER diamond
ENV DIAMOND_STATE=/state DIAMOND_TEST_DIR=/state/tests PYTHONDONTWRITEBYTECODE=1
EXPOSE 8765
ENTRYPOINT ["python", "-I", "-m", "originspan"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0"]

FROM runtime AS test
USER root
RUN pip install --no-cache-dir pytest==8.4.2
COPY tests ./tests
COPY scripts ./scripts
COPY render.yaml Dockerfile.render ./
COPY evaluations/fixtures ./evaluations/fixtures
USER diamond

FROM runtime AS production
