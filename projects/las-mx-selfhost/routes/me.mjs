import {json,owner,signedIn} from '../shared.mjs';
export function GET(r        ){return json({owner:owner(r),tracking:signedIn(r)})}
