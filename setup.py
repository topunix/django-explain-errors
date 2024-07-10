import os
from setuptools import find_packages, setup

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

# get README
with open('README.rst') as f:
    long_description = f.read()

setup(
    name='django-explain-errors',
    version='0.1',
    packages=find_packages(),
    description='Django middleware that captures errors and exceptions, sends them to OpenAI for a detailed explanation, and prints the explanation to stdout when debug mode is enabled',
    long_description_content_type="text/markdown",
    long_description = long_description,
    install_requires=['Django>=2'],
    url='https://github.com/topunix/django-explain-errors',
    author='topunix',
    author_email='topunixguy@gmail.com',
    license='MIT',
    classifiers=[
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 2.0',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Topic :: Internet :: WWW/HTTP',
    ],
)
