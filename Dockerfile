FROM public.ecr.aws/lambda/python:3.14

COPY requirements/ /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/requirements-lambda.txt

COPY . .

CMD ["config.asgi.handler"]
