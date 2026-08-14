package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"strings"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/textract"
	"github.com/aws/aws-sdk-go-v2/service/textract/types"
)

type Box struct {
	Bbox      [4]int `json:"bbox"`
	IsChecked bool   `json:"is_checked"`
}

type DetectResponse struct {
	Boxes []Box `json:"boxes"`
}

var textractClient *textract.Client

func init() {
	cfg, err := config.LoadDefaultConfig(context.Background())
	if err != nil {
		panic(fmt.Sprintf("failed to load AWS config: %v", err))
	}
	textractClient = textract.NewFromConfig(cfg)
}

func handleRequest(ctx context.Context, request events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	imageBytes, err := extractImageBytes(request)
	if err != nil {
		return jsonResponse(http.StatusBadRequest, map[string]string{"error": err.Error()})
	}

	width, height, err := decodeImageDimensions(imageBytes)
	if err != nil {
		return jsonResponse(http.StatusBadRequest, map[string]string{"error": fmt.Sprintf("invalid image: %v", err)})
	}

	boxes, err := detectCheckboxes(ctx, imageBytes, width, height)
	if err != nil {
		return jsonResponse(http.StatusInternalServerError, map[string]string{"error": err.Error()})
	}

	return jsonResponse(http.StatusOK, DetectResponse{Boxes: boxes})
}

// extractImageBytes pulls the uploaded document image out of the API Gateway
// request. It accepts either a raw image body or a multipart/form-data
// upload (any field containing a file part).
func extractImageBytes(request events.APIGatewayV2HTTPRequest) ([]byte, error) {
	contentType := headerValue(request.Headers, "content-type")
	if contentType == "" {
		return nil, fmt.Errorf("missing Content-Type header")
	}

	mediaType, params, err := mime.ParseMediaType(contentType)
	if err != nil {
		return nil, fmt.Errorf("invalid Content-Type: %w", err)
	}

	body := []byte(request.Body)
	if request.IsBase64Encoded {
		decoded, err := base64.StdEncoding.DecodeString(request.Body)
		if err != nil {
			return nil, fmt.Errorf("failed to decode request body: %w", err)
		}
		body = decoded
	}

	if !strings.HasPrefix(mediaType, "multipart/") {
		// Not multipart: treat the raw body itself as the image.
		return body, nil
	}

	boundary, ok := params["boundary"]
	if !ok {
		return nil, fmt.Errorf("multipart request missing boundary")
	}

	reader := multipart.NewReader(bytes.NewReader(body), boundary)
	for {
		part, err := reader.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("failed to read multipart body: %w", err)
		}
		if part.FileName() == "" {
			continue
		}
		data, err := io.ReadAll(part)
		if err != nil {
			return nil, fmt.Errorf("failed to read uploaded file: %w", err)
		}
		return data, nil
	}

	return nil, fmt.Errorf("no file found in multipart body")
}

// decodeImageDimensions reads just enough of the image to determine its
// pixel dimensions. A failure here means the client sent something that
// isn't a decodable JPEG/PNG, not a server-side problem.
func decodeImageDimensions(imageBytes []byte) (width, height float32, err error) {
	cfg, _, err := image.DecodeConfig(bytes.NewReader(imageBytes))
	if err != nil {
		return 0, 0, err
	}
	return float32(cfg.Width), float32(cfg.Height), nil
}

// detectCheckboxes calls Textract AnalyzeDocument with the FORMS feature
// type and converts every SELECTION_ELEMENT block (checkboxes and radio
// buttons) into a pixel-coordinate bounding box with its checked state.
func detectCheckboxes(ctx context.Context, imageBytes []byte, width, height float32) ([]Box, error) {
	out, err := textractClient.AnalyzeDocument(ctx, &textract.AnalyzeDocumentInput{
		Document: &types.Document{
			Bytes: imageBytes,
		},
		FeatureTypes: []types.FeatureType{types.FeatureTypeForms},
	})
	if err != nil {
		return nil, fmt.Errorf("textract AnalyzeDocument failed: %w", err)
	}

	boxes := []Box{}
	for _, block := range out.Blocks {
		if block.BlockType != types.BlockTypeSelectionElement {
			continue
		}
		if block.Geometry == nil || block.Geometry.BoundingBox == nil {
			continue
		}
		bb := block.Geometry.BoundingBox

		x1 := int(bb.Left * width)
		y1 := int(bb.Top * height)
		x2 := int((bb.Left + bb.Width) * width)
		y2 := int((bb.Top + bb.Height) * height)

		boxes = append(boxes, Box{
			Bbox:      [4]int{x1, y1, x2, y2},
			IsChecked: block.SelectionStatus == types.SelectionStatusSelected,
		})
	}

	return boxes, nil
}

func headerValue(headers map[string]string, key string) string {
	for k, v := range headers {
		if strings.EqualFold(k, key) {
			return v
		}
	}
	return ""
}

func jsonResponse(statusCode int, payload any) (events.APIGatewayV2HTTPResponse, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return events.APIGatewayV2HTTPResponse{StatusCode: http.StatusInternalServerError}, err
	}
	return events.APIGatewayV2HTTPResponse{
		StatusCode: statusCode,
		Headers:    map[string]string{"Content-Type": "application/json"},
		Body:       string(body),
	}, nil
}

func main() {
	lambda.Start(handleRequest)
}
