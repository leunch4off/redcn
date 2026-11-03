// Explicit assistant reviews remain separate from operator confirmation and
// local detector predictions. A changed original invalidates the old review.
import {PHOTO_FEATURE_IDS} from './photo-observation-policy.mjs';
const teacherExcludedFeatures=new Set(['bed','internet','closet','pantry','balcony','loggia','homeCinema','roomFurniture']);
const teacherAllowedFeatures=new Set(PHOTO_FEATURE_IDS.filter(id=>!teacherExcludedFeatures.has(id)));
const teacherReviewDigest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export function validTeacherReview(value,originalId){
 return !!(value&&value.version===1&&value.kind==='assistant-visual-review'&&value.humanVerified===false&&value.exhaustive===false&&teacherReviewDigest(value.originalId)&&value.originalId===originalId&&teacherReviewDigest(value.reviewId)&&teacherReviewDigest(value.datasetId)&&typeof value.reviewedAt==='string'&&value.reviewedAt.length>=10&&value.reviewedAt.length<=40&&Array.isArray(value.features)&&value.features.length<=teacherAllowedFeatures.size&&new Set(value.features).size===value.features.length&&value.features.every(id=>teacherAllowedFeatures.has(id)));
}
export function teacherReviewed(item,feature){
 return validTeacherReview(item?.teacherReview,item?.analysis?.originalId)&&item.teacherReview.features.includes(feature)&&item.rejected?.[feature]!==true;
}
export function teacherEvidence(item,feature){
 if(!teacherReviewed(item,feature))return null;
 const review=item.teacherReview;
 return {id:feature,status:'assistant-reviewed',sourceUrl:item.url,originalId:review.originalId,photoId:review.originalId,engine:'assistant-visual-review',autoEligible:false,count:null,humanVerified:false,exhaustive:false,reviewId:review.reviewId,datasetId:review.datasetId,reviewedAt:review.reviewedAt,evidence:[{kind:'assistant-visual-review',source:'assistant-visual-review',originalId:review.originalId,photoId:review.originalId,directlyVisible:true,humanVerified:false,exhaustive:false,reviewId:review.reviewId}]};
}
export function isTeacherEvidence(row){
 return !!(row&&row.status==='assistant-reviewed'&&teacherAllowedFeatures.has(row.id)&&teacherReviewDigest(row.originalId)&&row.photoId===row.originalId&&row.humanVerified===false&&row.exhaustive===false&&row.autoEligible===false&&row.count===null&&teacherReviewDigest(row.reviewId)&&teacherReviewDigest(row.datasetId)&&Array.isArray(row.evidence)&&row.evidence.some(e=>e?.kind==='assistant-visual-review'&&e.source==='assistant-visual-review'&&e.originalId===row.originalId&&e.photoId===row.originalId&&e.directlyVisible===true&&e.humanVerified===false&&e.exhaustive===false&&e.reviewId===row.reviewId));
}
