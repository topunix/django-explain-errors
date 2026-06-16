import os
from setuptools import find_packages, setup

# allow setup.py to be run from any path
os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

# get README
with open('README.md', encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='django-explain-errors',
    version='0.2.0',
    packages=find_packages(exclude=['tests', 'tests.*']),
    description='Django middleware that captures errors and exceptions, sends them to OpenAI for a detailed explanation, and prints the explanation to stdout when debug mode is enabled. Supports both sync and async views.',
    long_description_content_type='text/markdown',
    long_description=long_description,
    python_requires='>=3.9',
    install_requires=[
        'Django>=4.2',
        'openai>=1.0',
        'python-dotenv>=1.0',
        'asgiref>=3.6',
    ],
    url='https://github.com/topunix/django-explain-errors',
    author='topunix',
    author_email='topunixguy@gmail.com',
    license='MIT',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Framework :: Django',
        'Framework :: Django :: 4.2',
        'Framework :: Django :: 5.0',
        'Framework :: Django :: 5.1',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Topic :: Internet :: WWW/HTTP',
    ],
)
