const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync('modules/portainer-cookies.js', 'utf8').replace(/export default.*;/, ''), context);
for (const [input, expected] of [
    [undefined, ''], ['nixlab_session=secret', ''],
    ['nixlab_session=secret; portainer_auth=native-auth; nixlab_session=other', 'portainer_auth=native-auth'],
    ['nixlab_session_other=keep; native="a=b"', 'nixlab_session_other=keep; native="a=b"'],
]) assert.equal(context.requestCookies({headersIn: {Cookie: input}}), expected);
const native = 'portainer_auth=native-auth; Path=/; Secure; HttpOnly; SameSite=Strict';
const expiring = 'other=value; Expires=Wed, 21 Oct 2037 07:28:00 GMT';
for (const [input, expected] of [
    [undefined, undefined], ['nixlab_session=attacker; Path=/', undefined],
    [[native, 'nixlab_session=attacker; Path=/', expiring], [native, expiring]],
    [native, [native]],
]) {
    const r = {headersOut: {}};
    if (input !== undefined) r.headersOut['Set-Cookie'] = input;
    context.responseCookies(r);
    assert.equal(JSON.stringify(r.headersOut['Set-Cookie']), JSON.stringify(expected));
}
console.log('Portainer cookie isolation checks passed.');
