import React from 'react';
import {render} from 'ink';

import {App} from './App.js';
import type {FrontendConfig} from './types.js';

const config = JSON.parse(process.env.OPENHARNESS_FRONTEND_CONFIG ?? '{}') as FrontendConfig;

render(<App config={config} />);
