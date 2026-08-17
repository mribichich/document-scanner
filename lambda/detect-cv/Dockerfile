FROM public.ecr.aws/lambda/python:3.13

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY detect_cv.py handler.py textract_hints.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
