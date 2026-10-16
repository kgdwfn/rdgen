FROM docker.1ms.run/python:3.13-alpine

RUN adduser -D user
USER user

WORKDIR /opt/rdgen

COPY . .
RUN pip install --no-cache-dir -r requirements.txt \
 && python manage.py migrate

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget --spider 0.0.0.0:8000

# 用 python -m gunicorn，避免硬编码 bin 路径（不同基础镜像 pip 落盘位置不同会导致路径失效）
CMD ["python", "-m", "gunicorn", "-c", "gunicorn.conf.py", "rdgen.wsgi:application"]
