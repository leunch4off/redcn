import test from 'node:test';
import assert from 'node:assert/strict';
import {cloudBackupStatusText} from '../web/cloud-backup.mjs';
test('cloud backup state is explicit and default off, never infers upload from connection',()=>{assert.match(cloudBackupStatusText({version:1,configured:false,enabled:false}),/не подключено/u);assert.match(cloudBackupStatusText({version:1,configured:true,enabled:false}),/Сохраните ключ/u);assert.match(cloudBackupStatusText({version:1,configured:true,enabled:true}),/только по кнопке/u);assert.throws(()=>cloudBackupStatusText({configured:true,enabled:true}));});
