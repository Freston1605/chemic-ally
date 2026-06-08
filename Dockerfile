FROM public.ecr.aws/lambda/python:3.14

COPY requirements/requirements-lambda.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . .

CMD ["config.asgi.handler"]
