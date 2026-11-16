const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync('modules/arcane-cookies.js', 'utf8').replace(/export default.*;/, ''), context);
for (const [input, expected] of [
    [undefined, ''], ['__Host-nixlab_bootstrap=private; arcane_auth=native-auth', 'arcane_auth=native-auth'], ['nixlab_session=secret', ''],
    ['nixlab_session=secret; arcane_auth=native-auth; nixlab_session=other', 'arcane_auth=native-auth'],
    ['nixlab_session_other=keep; native="a=b"', 'nixlab_session_other=keep; native="a=b"'],
]) assert.equal(context.requestCookies({headersIn: {Cookie: input}}), expected);
const native = 'arcane_auth=native-auth; Path=/; Secure; HttpOnly; SameSite=Strict';
const expiring = 'other=value; Expires=Wed, 21 Oct 2037 07:28:00 GMT';
for (const [input, expected] of [
    [undefined, undefined], ['__Host-nixlab_bootstrap=private; Path=/', undefined], ['nixlab_session=attacker; Path=/', undefined],
    [[native, 'nixlab_session=attacker; Path=/', expiring], [native, expiring]],
    [native, [native]],
]) {
    const r = {headersOut: {}};
    if (input !== undefined) r.headersOut['Set-Cookie'] = input;
    context.responseCookies(r);
    assert.equal(JSON.stringify(r.headersOut['Set-Cookie']), JSON.stringify(expected));
}
console.log('Arcane cookie isolation checks passed.');
