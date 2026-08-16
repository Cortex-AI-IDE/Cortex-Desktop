# For legal and security concerns, we typically only allow Web Fetch to access
# domains that the user has provided in some form. However, we make an
# exception for a list of preapproved domains that are code-related.
#
# SECURITY WARNING: These preapproved domains are ONLY for WebFetch (GET requests only).
# The sandbox system deliberately does NOT inherit this list for network restrictions,
# as arbitrary network access (POST, uploads, etc.) to these domains could enable
# data exfiltration. Some domains like huggingface.co, kaggle.com, and nuget.org
# allow file uploads and would be dangerous for unrestricted network access.
#
# See test/utils/sandbox/webfetch-preapproved-separation.test.ts for verification
# that sandbox network restrictions require explicit user permission rules.

PREAPPROVED_HOSTS = {
    # Anthropic
    'platform.cortex.com',
    'code.cortex.com',
    'modelcontextprotocol.io',
    'github.com/anthropics',
    'agentskills.io',

    # Top Programming Languages
    'docs.python.org',  # Python
    'en.cppreference.com',  # C/C++ reference
    'docs.oracle.com',  # Java
    'learn.microsoft.com',  # C#/.NET
    'developer.mozilla.org',  # JavaScript/Web APIs (MDN)
    'go.dev',  # Go
    'pkg.go.dev',  # Go docs
    'www.php.net',  # PHP
    'docs.swift.org',  # Swift
    'kotlinlang.org',  # Kotlin
    'ruby-doc.org',  # Ruby
    'doc.rust-lang.org',  # Rust
    'www.typescriptlang.org',  # TypeScript

    # Web & JavaScript Frameworks/Libraries
    'react.dev',  # React
    'angular.io',  # Angular
    'vuejs.org',  # Vue.js
    'nextjs.org',  # Next.js
    'expressjs.com',  # Express.js
    'nodejs.org',  # Node.js
    'bun.sh',  # Bun
    'jquery.com',  # jQuery
    'getbootstrap.com',  # Bootstrap
    'tailwindcss.com',  # Tailwind CSS
    'd3js.org',  # D3.js
    'threejs.org',  # Three.js
    'redux.js.org',  # Redux
    'webpack.js.org',  # Webpack
    'jestjs.io',  # Jest
    'reactrouter.com',  # React Router

    # Python Frameworks & Libraries
    'docs.djangoproject.com',  # Django
    'flask.palletsprojects.com',  # Flask
    'fastapi.tiangolo.com',  # FastAPI
    'pandas.pydata.org',  # Pandas
    'numpy.org',  # NumPy
    'www.tensorflow.org',  # TensorFlow
    'pytorch.org',  # PyTorch
    'scikit-learn.org',  # Scikit-learn
    'matplotlib.org',  # Matplotlib
    'requests.readthedocs.io',  # Requests
    'jupyter.org',  # Jupyter

    # PHP Frameworks
    'laravel.com',  # Laravel
    'symfony.com',  # Symfony
    'wordpress.org',  # WordPress

    # Java Frameworks & Libraries
    'docs.spring.io',  # Spring
    'hibernate.org',  # Hibernate
    'tomcat.apache.org',  # Tomcat
    'gradle.org',  # Gradle
    'maven.apache.org',  # Maven

    # .NET & C# Frameworks
    'asp.net',  # ASP.NET
    'dotnet.microsoft.com',  # .NET
    'nuget.org',  # NuGet
    'blazor.net',  # Blazor

    # Mobile Development
    'reactnative.dev',  # React Native
    'docs.flutter.dev',  # Flutter
    'developer.apple.com',  # iOS/macOS
    'developer.android.com',  # Android

    # Data Science & Machine Learning
    'keras.io',  # Keras
    'spark.apache.org',  # Apache Spark
    'huggingface.co',  # Hugging Face
    'www.kaggle.com',  # Kaggle

    # Databases
    'www.mongodb.com',  # MongoDB
    'redis.io',  # Redis
    'www.postgresql.org',  # PostgreSQL
    'dev.mysql.com',  # MySQL
    'www.sqlite.org',  # SQLite
    'graphql.org',  # GraphQL
    'prisma.io',  # Prisma

    # Cloud & DevOps
    'docs.aws.amazon.com',  # AWS
    'cloud.google.com',  # Google Cloud
    'learn.microsoft.com',  # Azure
    'kubernetes.io',  # Kubernetes
    'www.docker.com',  # Docker
    'www.terraform.io',  # Terraform
    'www.ansible.com',  # Ansible
    'vercel.com/docs',  # Vercel
    'docs.netlify.com',  # Netlify
    'devcenter.heroku.com',  # Heroku

    # Testing & Monitoring
    'cypress.io',  # Cypress
    'selenium.dev',  # Selenium

    # Game Development
    'docs.unity.com',  # Unity
    'docs.unrealengine.com',  # Unreal Engine

    # Other Essential Tools
    'git-scm.com',  # Git
    'nginx.org',  # Nginx
    'httpd.apache.org',  # Apache HTTP Server
}

# Split once at module load so lookups are O(1) set membership for the common
# hostname-only case, falling back to a small per-host path-prefix dict
# for the handful of path-scoped entries (e.g., "github.com/anthropics").


def _build_lookup_tables():
    """Build optimized lookup tables from PREAPPROVED_HOSTS."""
    hosts = set()
    paths = {}
    
    for entry in PREAPPROVED_HOSTS:
        slash_idx = entry.find('/')
        if slash_idx == -1:
            hosts.add(entry)
        else:
            host = entry[:slash_idx]
            path = entry[slash_idx:]
            if host in paths:
                paths[host].append(path)
            else:
                paths[host] = [path]
    
    return hosts, paths


HOSTNAME_ONLY, PATH_PREFIXES = _build_lookup_tables()


def is_preapproved_host(hostname: str, pathname: str) -> bool:
    """
    Check if a hostname/pathname combination is preapproved for WebFetch.
    
    Args:
        hostname: The hostname to check (e.g., 'github.com')
        pathname: The URL path (e.g., '/anthropics/repo')
    
    Returns:
        True if the host is preapproved, False otherwise
    """
    if hostname in HOSTNAME_ONLY:
        return True
    
    prefixes = PATH_PREFIXES.get(hostname)
    if prefixes:
        for p in prefixes:
            # Enforce path segment boundaries: "/anthropics" must not match
            # "/anthropics-evil/malware". Only exact match or a "/" after the
            # prefix is allowed.
            if pathname == p or pathname.startswith(p + '/'):
                return True
    
    return False
