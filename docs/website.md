# Website and custom domain

The public landing page and documentation live at `nixlab.dardery.work`.
This is a static GitHub Pages site. It has no access to your server, credentials
or management APIs. An impressively safe amount of remote administration: none.

## Connect nixlab.dardery.work

Adding or verifying `dardery.work` in your GitHub account establishes ownership.
The repository still needs its own publishing source and custom-domain setting.

1. Merge the website changes into the repository's `main` branch.
2. Open the `OmarDardery/nixlab` repository's **Settings → Pages**.
3. Under **Build and deployment → Source**, choose **GitHub Actions**.
4. Under **Custom domain**, enter `nixlab.dardery.work` and save.
5. In the DNS provider for `dardery.work`, add this record:

| Type | Name | Target | Proxy |
|---|---|---|---|
| CNAME | `nixlab` | `omardardery.github.io` | DNS only during setup, if using Cloudflare |

The target has **no protocol, path or repository name**. Do not point it at
`omardardery.github.io/nixlab`, your home server or your apex domain. Leave the
apex and unrelated subdomains alone. Replace only conflicting records for the
exact `nixlab` hostname.

6. Run **Actions → Website → Run workflow** on `main` if a deployment has not
   already started. Later changes to docs or website files deploy automatically.
7. Once GitHub accepts the DNS configuration and provisions the certificate,
   enable **Enforce HTTPS** in Pages settings. Certificate provisioning is not instant.

For Actions-based publication, GitHub's **Custom domain** setting controls routing;
adding a `CNAME` file to the artifact does not configure that setting.

Official references: [custom subdomains](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site),
[Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[HTTPS](https://docs.github.com/en/pages/getting-started-with-github-pages/securing-your-github-pages-site-with-https),
[Cloudflare DNS-only records](https://developers.cloudflare.com/dns/proxy-status/).

## If the domain is not working

- **GitHub Pages 404:** check the latest Website deployment, repository Pages
  source and exact custom-domain spelling. Domain verification alone does not publish a site.
- **DNS check fails:** inspect the exact `nixlab` record. It should resolve through
  `omardardery.github.io`; remove competing records for that hostname and allow
  the provider's DNS TTL to expire. With Cloudflare, use DNS only during validation.
- **HTTPS is unavailable:** wait for Pages certificate provisioning and check the
  DNS guidance above. Do not disable TLS validation as a workaround.
- **Styles or docs return 404:** the build needs the correct base path. The workflow
  gets it from `actions/configure-pages`; a custom domain uses the root, while a
  project site can use `/nixlab`. Both layouts are checked during builds.
- **The page describes code not yet on main:** merge the implementation changes
  before treating the canonical installation command as updated.

## Edit the site

`website/index.html` owns the landing page, `website/assets/` owns its CSS and
browser behavior, and `website/build.py` renders the actual `docs/*.md` into
searchable documentation. Markdown remains the source of truth for both the
website and the offline guides in the management portal.

```sh
python3 -m venv .venv-website
.venv-website/bin/pip install -r website/requirements.txt
.venv-website/bin/python website/build.py --check
```

The output is `_site/`. Preview it with any static server. To check the project-site
layout, pass `--base-path /nixlab --output /tmp/nixlab-project-site --check`.
The build checks internal links, anchors, assets and the canonical install command.
Search runs in the browser against a generated index; no analytics, login or backend
is required for the public website.
