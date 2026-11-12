import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../web/hub.js', import.meta.url), 'utf8');

for (const outcome of ['rejected', 'network-error', 'opened']) {
  test(`project center visibility after project ${outcome}`, async () => {
    const timers = [], errors = [], classes = new Set(['on']);
    const hub = {
      style: {display: 'flex'},
      classList: {
        add: name => classes.add(name),
        remove: name => classes.delete(name),
        contains: name => classes.has(name),
      },
    };
    const context = vm.createContext({
      console, S: {}, window: {addEventListener() {}},
      $: id => id === 'projectHub' ? hub : null,
      setTimeout: callback => timers.push(callback),
      setLoading() {},
      showErr: message => errors.push(message),
      fetch: async path => {
        if (path === '/api/project/open') {
          if (outcome === 'network-error') throw new Error('connection lost');
          return {json: async () => outcome === 'opened'
            ? {ok: true, world: {name: 'valid world'}}
            : {ok: false, error: 'missing region'}};
        }
        return {json: async () => path === '/api/projects' ? {projects: []} : []};
      },
    });
    vm.runInContext(source, context);
    vm.runInContext('updateWorldInfoUI = () => {}; renderSpawnPointDefault = () => {};', context);

    await vm.runInContext('launchProject({world: "chosen-world"})', context);
    for (const callback of timers) callback();

    assert.equal(hub.style.display, outcome === 'opened' ? 'none' : 'flex');
    assert.equal(errors.length, outcome === 'opened' ? 0 : 1);
  });
}
