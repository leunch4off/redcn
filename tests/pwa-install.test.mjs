import test from 'node:test';
import assert from 'node:assert/strict';
import {webAppInstalled,pwaInstallPlatform} from '../web/pwa-install.mjs';

test('install control hides only for actual app mode or appinstalled evidence',()=>{
  assert.equal(webAppInstalled(),false);
  assert.equal(webAppInstalled({displayMode:'browser',standalone:false}),false);
  assert.equal(webAppInstalled({standalone:true}),true);
  assert.equal(webAppInstalled({displayMode:'standalone'}),true);
  assert.equal(webAppInstalled({displayMode:'minimal-ui'}),true);
  assert.equal(webAppInstalled({appInstalled:true}),true);
  assert.equal(webAppInstalled({clickedInstall:true,acceptedPrompt:true,localStorageInstalled:true}),false);
});

test('iPhone and touch iPad get manual Home Screen guidance without misclassifying Mac',()=>{
  assert.equal(pwaInstallPlatform({userAgent:'Mozilla iPhone OS 18 Safari'}),'ios');
  assert.equal(pwaInstallPlatform({userAgent:'Safari',platform:'MacIntel',maxTouchPoints:5}),'ios');
  assert.equal(pwaInstallPlatform({userAgent:'Safari',platform:'MacIntel',maxTouchPoints:0}),'other');
  assert.equal(pwaInstallPlatform({userAgent:'Android Chrome'}),'other');
});
