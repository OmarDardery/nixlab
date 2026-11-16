// Cookies are shared across ports. Keep the host console session on the host.
const portalCookies = ['nixlab_session', '__Host-nixlab_bootstrap'];

function cookieName(value) {
    return value.split('=', 1)[0].trim();
}

function requestCookies(r) {
    return (r.headersIn.Cookie || '').split(';').map(value => value.trim())
        .filter(value => value && !portalCookies.includes(cookieName(value))).join('; ');
}

function responseCookies(r) {
    const values = r.headersOut['Set-Cookie'];
    if (!values) return;
    const cookies = (Array.isArray(values) ? values : [values])
        .filter(value => !portalCookies.includes(cookieName(value)));
    if (cookies.length) r.headersOut['Set-Cookie'] = cookies;
    else delete r.headersOut['Set-Cookie'];
}

export default { requestCookies, responseCookies };
